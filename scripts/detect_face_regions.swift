import AVFoundation
import CoreGraphics
import Foundation
import Vision

struct FaceDetection: Codable {
    let sample: Int
    let time: Double
    let x: Double
    let y: Double
    let width: Double
    let height: Double
    let confidence: Float
}

struct DetectionResult: Codable {
    let duration: Double
    let sampleCount: Int
    let detections: [FaceDetection]
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

guard CommandLine.arguments.count >= 2 else {
    fail("Usage: detect_face_regions VIDEO [SAMPLE_COUNT]")
}

let videoURL = URL(fileURLWithPath: CommandLine.arguments[1])
let requestedSamples = CommandLine.arguments.count >= 3
    ? (Int(CommandLine.arguments[2]) ?? 18)
    : 18
let sampleCount = min(max(requestedSamples, 6), 40)
let asset = AVURLAsset(url: videoURL)
let duration = CMTimeGetSeconds(asset.duration)

guard duration.isFinite, duration > 0 else {
    fail("Could not read video duration")
}

let generator = AVAssetImageGenerator(asset: asset)
generator.appliesPreferredTrackTransform = true
generator.maximumSize = CGSize(width: 960, height: 960)
generator.requestedTimeToleranceBefore = CMTime(seconds: 0.2, preferredTimescale: 600)
generator.requestedTimeToleranceAfter = CMTime(seconds: 0.2, preferredTimescale: 600)

var detections: [FaceDetection] = []

for sample in 0..<sampleCount {
    // Avoid title cards and end cards, which are less likely to contain the camera tile.
    let fraction = 0.05 + (0.90 * Double(sample) / Double(max(sampleCount - 1, 1)))
    let seconds = min(max(duration * fraction, 0), max(duration - 0.001, 0))
    let requestedTime = CMTime(seconds: seconds, preferredTimescale: 600)

    do {
        var actualTime = CMTime.zero
        let image = try generator.copyCGImage(at: requestedTime, actualTime: &actualTime)
        let request = VNDetectFaceRectanglesRequest()
        let handler = VNImageRequestHandler(cgImage: image, orientation: .up, options: [:])
        try handler.perform([request])

        for observation in request.results ?? [] {
            let box = observation.boundingBox
            // Vision uses a bottom-left origin; FFmpeg crop uses top-left.
            detections.append(FaceDetection(
                sample: sample,
                time: CMTimeGetSeconds(actualTime),
                x: box.minX,
                y: 1.0 - box.maxY,
                width: box.width,
                height: box.height,
                confidence: observation.confidence
            ))
        }
    } catch {
        // A failed sample should not abort detection for the rest of the video.
        continue
    }
}

let result = DetectionResult(
    duration: duration,
    sampleCount: sampleCount,
    detections: detections
)

do {
    let encoder = JSONEncoder()
    let data = try encoder.encode(result)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    fail("Could not encode detection result: \(error)")
}
