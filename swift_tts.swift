#!/usr/bin/env swift
import AVFoundation
import Foundation

class SpeechSynthesizerDelegate: NSObject, AVSpeechSynthesizerDelegate {
    var isFinished = false
    
    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        isFinished = true
    }
}

// Command line argument handling
let args = CommandLine.arguments
guard args.count > 1 else {
    print("Usage: swift_tts.swift [--speed <multiplier>] <text>")
    exit(1)
}

var speedMultiplier = 1.0
var tokens: [String] = []

var i = 1
while i < args.count {
    let arg = args[i]
    if arg == "--speed" {
        guard i + 1 < args.count else {
            print("Error: --speed requires a value")
            exit(2)
        }
        guard let parsed = Double(args[i + 1]), parsed.isFinite, parsed > 0 else {
            print("Error: invalid --speed value (must be a positive number)")
            exit(2)
        }
        speedMultiplier = parsed
        i += 2
        continue
    }
    tokens.append(arg)
    i += 1
}

guard !tokens.isEmpty else {
    print("Error: text is required")
    exit(2)
}

// Join remaining arguments to form the sentence
let text = tokens.joined(separator: " ")

let synthesizer = AVSpeechSynthesizer()
let utterance = AVSpeechUtterance(string: text)
let delegate = SpeechSynthesizerDelegate()
synthesizer.delegate = delegate

// By not setting a voice, we let the system use its default voice.
// utterance.voice = AVSpeechSynthesisVoice(language: "ja-JP")

// Configure rate (speedMultiplier=1.0 keeps the default).
let baseRate = AVSpeechUtteranceDefaultSpeechRate
let desiredRate = Float(Double(baseRate) * speedMultiplier)
utterance.rate = min(max(desiredRate, AVSpeechUtteranceMinimumSpeechRate), AVSpeechUtteranceMaximumSpeechRate)

synthesizer.speak(utterance)

// Helper to keep the script running until speech finishes
let runLoop = RunLoop.current
while !delegate.isFinished {
    runLoop.run(mode: .default, before: Date(timeIntervalSinceNow: 0.1))
}
