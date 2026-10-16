// SPDX-License-Identifier: MIT
import Foundation

let segmentBytes = 1_048_576
let clusterWidth = 256
let clusterHeaderBytes = clusterWidth * 72 + 32
let maxFileBytes = 64 * 1024 * 1024

struct RangeRecord: Codable, Hashable, Sendable {
  var offset: Int
  var size: Int
  var sha256: String
  func valid(limit: Int, maximum: Int) -> Bool {
    offset >= 0 && size > 0 && size <= maximum && offset <= limit - size && validHash(sha256)
  }
}
struct ClusterRecord: Codable, Sendable {
  var offset: Int
  var size: Int
  var sha256: String
  var cluster: Int
  var expected_mac: String
  var record: RangeRecord { RangeRecord(offset: offset, size: size, sha256: sha256) }
}
struct ImageRecord: Codable, Sendable { var size_bytes: Int; var sha256: String }
struct SourceRecord: Codable, Sendable { var size_bytes: Int; var sha256: String; var url: String }
struct SystemRecord: Codable, Sendable {
  var size_bytes: Int
  var sha256: String
  var member: String
  var decoded: ImageRecord
}
struct PlainSpan: Codable, Sendable { var offset: Int; var size: Int }
struct StorageRecord: Codable, Sendable { var size: Int; var sha256: String; var spans: [PlainSpan] }
struct RangeFile: Codable, Sendable {
  var source: String
  var name: String
  var transform: String
  var compressed: Bool
  var raw_sha256: String
  var sha256: String
  var size: Int
  var storage: [StorageRecord]
}
struct RangeRecipe: Codable, Sendable {
  var schema: Int
  var source: SourceRecord
  var system_image: SystemRecord
  var member_offset: Int
  var zip_header: RangeRecord
  var prefix: RangeRecord
  var headers: [ClusterRecord]
  var segments: [String: RangeRecord]
  var files: [RangeFile]

  func validate() throws {
    let system = system_image
    try require(
      schema == 1 && appleRangeURL(source.url) && source.size_bytes > 0 && source.size_bytes <= 128 << 30
        && validHash(source.sha256) && validHash(system.sha256) && validHash(system.decoded.sha256)
        && system.size_bytes > 0 && system.size_bytes <= source.size_bytes
        && system.decoded.size_bytes > 0 && system.decoded.size_bytes <= 128 << 30
        && safeRelative(system.member) && member_offset >= 0
        && member_offset <= source.size_bytes - system.size_bytes
        && zip_header.valid(limit: source.size_bytes, maximum: 65536)
        && zip_header.offset + zip_header.size == member_offset
        && prefix.valid(limit: system.size_bytes, maximum: segmentBytes + 156)
        && prefix.offset == 0 && prefix.size >= 156
        && (1...128).contains(headers.count) && (1...128).contains(segments.count)
        && (1...128).contains(files.count), "Invalid firmware recipe identity or resource bounds.")
    var clusters = Set<Int>()
    var encrypted = 0
    for header in headers {
      try require(
        header.cluster >= 0
          && header.cluster < (system.decoded.size_bytes + segmentBytes * clusterWidth - 1) / (segmentBytes * clusterWidth)
          && !clusters.contains(header.cluster) && header.size == clusterHeaderBytes
          && header.record.valid(limit: system.size_bytes, maximum: clusterHeaderBytes)
          && header.offset >= prefix.size && validHash(header.expected_mac), "Invalid firmware cluster descriptor.")
      clusters.insert(header.cluster)
      encrypted += header.size
    }
    var ids = Set<Int>()
    for (key, record) in segments {
      guard let id = Int(key), id >= 0, String(id) == key,
        id < (system.decoded.size_bytes + segmentBytes - 1) / segmentBytes,
        clusters.contains(id / clusterWidth), record.valid(limit: system.size_bytes, maximum: segmentBytes)
      else { throw RestoreFailure("Invalid firmware segment descriptor.") }
      ids.insert(id)
      encrypted += record.size
    }
    try require(encrypted <= maxFileBytes, "Firmware network budget exceeded.")
    var names = Set<String>()
    var needed = Set<Int>()
    var neededClusters = Set<Int>()
    var output = 0
    var storageTotal = 0
    for file in files {
      try require(
        safeRelative(file.source) && safeRelative(file.name) && !names.contains(file.name)
          && validHash(file.raw_sha256) && validHash(file.sha256) && (0...maxFileBytes).contains(file.size)
          && ["identity", "nvram"].contains(file.transform) && (1...2).contains(file.storage.count)
          && (file.compressed || file.storage.count == 1), "Invalid firmware file descriptor.")
      try require(!names.contains { file.name.hasPrefix($0 + "/") || $0.hasPrefix(file.name + "/") },
                  "Conflicting output paths.")
      names.insert(file.name)
      output += file.size
      for storage in file.storage {
        try require((0...maxFileBytes).contains(storage.size) && validHash(storage.sha256)
                      && storage.spans.count <= 4096, "Invalid firmware storage descriptor.")
        storageTotal += storage.size
        var size = 0
        for span in storage.spans {
          try require(span.offset >= 0 && span.size > 0 && span.size <= storage.size
                        && span.offset <= system.decoded.size_bytes - span.size, "Invalid firmware plaintext span.")
          size += span.size
          try require(size <= storage.size, "Firmware span sum exceeded.")
          for id in (span.offset / segmentBytes)...((span.offset + span.size - 1) / segmentBytes) {
            try require(ids.contains(id), "Missing firmware segment dependency.")
            needed.insert(id)
            neededClusters.insert(id / clusterWidth)
          }
        }
        try require(size == storage.size, "Firmware span sum mismatch.")
      }
    }
    try require(output <= maxFileBytes && storageTotal <= maxFileBytes && needed == ids && neededClusters == clusters,
                "Firmware output budget or dependency closure mismatch.")
  }
}

// JSONDecoder ignores unknown keys; explicitly check the complete recipe schema first.
func strictRecipe(_ bytes: Data) throws -> RangeRecipe {
  func object(_ value: Any?, _ keys: Set<String>) throws -> [String: Any] {
    guard let dict = value as? [String: Any], Set(dict.keys) == keys else {
      throw RestoreFailure("Unknown or missing firmware recipe fields.")
    }
    return dict
  }
  func array(_ value: Any?) throws -> [Any] {
    guard let result = value as? [Any] else { throw RestoreFailure("Invalid recipe array.") }
    return result
  }
  let r = try object(JSONSerialization.jsonObject(with: bytes), ["schema", "source", "system_image", "member_offset", "zip_header", "prefix", "headers", "segments", "files"])
  _ = try object(r["source"], ["url", "size_bytes", "sha256"])
  let system = try object(r["system_image"], ["member", "size_bytes", "sha256", "decoded"])
  _ = try object(system["decoded"], ["size_bytes", "sha256"])
  for key in ["zip_header", "prefix"] { _ = try object(r[key], ["offset", "size", "sha256"]) }
  for h in try array(r["headers"]) { _ = try object(h, ["offset", "size", "sha256", "cluster", "expected_mac"]) }
  guard let segments = r["segments"] as? [String: Any] else { throw RestoreFailure("Invalid segments.") }
  for s in segments.values { _ = try object(s, ["offset", "size", "sha256"]) }
  for value in try array(r["files"]) {
    let f = try object(value, ["source", "name", "transform", "compressed", "raw_sha256", "sha256", "size", "storage"])
    for value in try array(f["storage"]) {
      let storage = try object(value, ["size", "sha256", "spans"])
      for span in try array(storage["spans"]) { _ = try object(span, ["offset", "size"]) }
    }
  }
  let recipe = try JSONDecoder().decode(RangeRecipe.self, from: bytes)
  try recipe.validate()
  return recipe
}
func readRecipe(_ path: URL, digest: String) throws -> RangeRecipe {
  try require(validHash(digest), "Invalid recipe SHA-256.")
  let bytes = try readRegular(path, maximum: 1024 * 1024)
  try require(bytes.sha256 == digest, "Firmware recipe SHA-256 mismatch.")
  return try strictRecipe(bytes)
}
