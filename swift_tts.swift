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
    print("Usage: swift_tts.swift <text>")
    exit(1)
}

// Join arguments to form the sentence
let text = args[1...].joined(separator: " ")

let synthesizer = AVSpeechSynthesizer()
let utterance = AVSpeechUtterance(string: text)
let delegate = SpeechSynthesizerDelegate()
synthesizer.delegate = delegate

// By not setting a voice, we let the system use its default voice.
// utterance.voice = AVSpeechSynthesisVoice(language: "ja-JP")

// Configure rate if needed
utterance.rate = AVSpeechUtteranceDefaultSpeechRate

synthesizer.speak(utterance)

// Helper to keep the script running until speech finishes
let runLoop = RunLoop.current
while !delegate.isFinished {
    runLoop.run(mode: .default, before: Date(timeIntervalSinceNow: 0.1))
}
