import Foundation
import Vision
import AppKit

func fail(_ message: String, code: Int32 = 2) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

guard CommandLine.arguments.count >= 2 else {
    fail("usage: food_ocr <image-path>")
}

let path = CommandLine.arguments[1]
let url = URL(fileURLWithPath: path)
guard let nsImage = NSImage(contentsOf: url) else {
    fail("cannot open image")
}

var rect = NSRect(origin: .zero, size: nsImage.size)
guard let cgImage = nsImage.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
    fail("cannot create CGImage")
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
if #available(macOS 11.0, *) {
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
}

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
do {
    try handler.perform([request])
} catch {
    fail("Vision OCR failed: \(error)")
}

let observations = request.results ?? []
for observation in observations {
    if let candidate = observation.topCandidates(1).first {
        let text = candidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
        if !text.isEmpty {
            print(text)
        }
    }
}
