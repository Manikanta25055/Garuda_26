// MJPEGStreamView.swift — MJPEG stream player using NSViewRepresentable
// Parses multipart/x-mixed-replace boundary stream from RPi FastAPI /stream endpoint
import SwiftUI
import AppKit
import Combine

// MARK: - MJPEG Receiver (URLSession data delegate)
final class MJPEGStreamReceiver: NSObject, URLSessionDataDelegate, ObservableObject {

    // All state declared at top
    @Published var currentFrame: NSImage? = nil
    @Published var isConnected: Bool = false

    private var buffer: Data = Data()
    private var urlSession: URLSession? = nil
    private var dataTask: URLSessionDataTask? = nil

    // JPEG start-of-image / end-of-image markers
    private let soiMarker: [UInt8] = [0xFF, 0xD8]
    private let eoiMarker: [UInt8] = [0xFF, 0xD9]

    // MARK: - Start / Stop
    func start(url: URL) {
        stop()
        buffer = Data()
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest  = GarudaConstants.streamTimeout
        config.timeoutIntervalForResource = 0  // no resource timeout (continuous stream)
        urlSession = URLSession(configuration: config, delegate: self, delegateQueue: nil)
        dataTask = urlSession?.dataTask(with: url)
        dataTask?.resume()
        DispatchQueue.main.async { self.isConnected = true }
    }

    func stop() {
        dataTask?.cancel()
        dataTask = nil
        urlSession?.invalidateAndCancel()
        urlSession = nil
        buffer = Data()
        DispatchQueue.main.async {
            self.isConnected = false
            self.currentFrame = nil
        }
    }

    // MARK: - URLSessionDataDelegate
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        buffer.append(data)
        extractFrames()
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        DispatchQueue.main.async { self.isConnected = false }
    }

    // MARK: - Frame Extraction
    private func extractFrames() {
        // Scan for JPEG SOI (FF D8) and EOI (FF D9) markers in the buffer
        var searchRange = buffer.startIndex..<buffer.endIndex
        while searchRange.lowerBound < buffer.endIndex {
            guard let soiRange = buffer.range(
                    of: Data(soiMarker), options: [], in: searchRange) else { break }
            guard let eoiRange = buffer.range(
                    of: Data(eoiMarker), options: [],
                    in: soiRange.upperBound..<buffer.endIndex) else { break }

            let jpegData = buffer[soiRange.lowerBound..<eoiRange.upperBound]
            if let image = NSImage(data: jpegData) {
                let captured = image
                DispatchQueue.main.async { self.currentFrame = captured }
            }
            searchRange = eoiRange.upperBound..<buffer.endIndex
            // Trim processed data to avoid unbounded growth
            buffer = buffer[searchRange]
            searchRange = buffer.startIndex..<buffer.endIndex
        }
    }
}

// MARK: - NSImageView Wrapper (NSViewRepresentable)
struct MJPEGStreamView: NSViewRepresentable {
    @ObservedObject var receiver: MJPEGStreamReceiver
    var url: URL?

    func makeNSView(context: Context) -> NSImageView {
        let view = NSImageView()
        view.imageScaling = .scaleProportionallyUpOrDown
        view.imageAlignment = .alignCenter
        view.animates = false
        view.wantsLayer = true
        view.layer?.backgroundColor = NSColor(GarudaTheme.bgSurface1).cgColor
        return view
    }

    func updateNSView(_ nsView: NSImageView, context: Context) {
        nsView.image = receiver.currentFrame
    }

    static func dismantleNSView(_ nsView: NSImageView, coordinator: ()) {
        // nothing needed
    }
}

// MARK: - Convenience wrapper View
struct LiveStreamView: View {
    let url: URL?

    @StateObject private var receiver = MJPEGStreamReceiver()

    var body: some View {
        ZStack {
            GarudaTheme.bgSurface1
            if receiver.currentFrame != nil {
                MJPEGStreamView(receiver: receiver, url: url)
            } else {
                streamPlaceholder
            }
        }
        .onAppear {
            if let url { receiver.start(url: url) }
        }
        .onDisappear {
            receiver.stop()
        }
        .onChange(of: url) { _, newURL in
            receiver.stop()
            if let newURL { receiver.start(url: newURL) }
        }
    }

    private var streamPlaceholder: some View {
        VStack(spacing: 12) {
            Image(systemName: "video.slash")
                .font(.system(size: 36))
                .foregroundColor(GarudaTheme.textQuaternary)
            Text(url == nil ? "No stream URL configured" : "Connecting to stream…")
                .font(GarudaFont.mono(size: 12))
                .foregroundColor(GarudaTheme.textTertiary)
        }
    }
}
