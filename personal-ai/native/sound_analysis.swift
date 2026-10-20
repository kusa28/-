import Foundation
import SoundAnalysis
import CoreMedia

final class Observer: NSObject, SNResultsObserving {
    var results: [[String: Any]] = []
    var failure: Error?

    func request(_ request: any SNRequest, didProduce result: any SNResult) {
        guard let result = result as? SNClassificationResult else { return }
        let start = CMTimeGetSeconds(result.timeRange.start)
        let duration = CMTimeGetSeconds(result.timeRange.duration)
        guard start.isFinite, duration.isFinite, start >= 0, duration > 0 else { return }
        let predictions: [[String: Any]] = result.classifications.prefix(3).map {
            ["identifier": $0.identifier, "confidence": $0.confidence]
        }
        results.append(["start": start, "end": start + duration,
                        "predictions": predictions])
    }

    func request(_ request: any SNRequest, didFailWithError error: any Error) {
        failure = error
    }
}

guard CommandLine.arguments.count == 2 else {
    fputs("usage: sound_analysis AUDIO_FILE\n", stderr)
    exit(2)
}

do {
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    let analyzer = try SNAudioFileAnalyzer(url: url)
    let request = try SNClassifySoundRequest(classifierIdentifier: .version1)
    let observer = Observer()
    try analyzer.add(request, withObserver: observer)
    analyzer.analyze()
    if let error = observer.failure { throw error }
    let output: [String: Any] = ["model": "Apple SoundAnalysis v1",
                                 "kind": "model_prediction",
                                 "windows": observer.results]
    let data = try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([10]))
} catch {
    fputs("SoundAnalysis failed: \(error.localizedDescription)\n", stderr)
    exit(1)
}
