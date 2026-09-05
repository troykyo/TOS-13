import Foundation

/// `NSRegularExpression` rather than Swift 5.7 `Regex` literals, so the package
/// builds on older toolchains without a language-mode dance.
enum Re {
    static func compile(_ pattern: String) -> NSRegularExpression {
        // Patterns here are compile-time constants; a failure is a programming
        // error, not a runtime condition.
        try! NSRegularExpression(pattern: pattern, options: [.caseInsensitive])
    }

    static func matches(_ regex: NSRegularExpression, _ s: String) -> Bool {
        regex.firstMatch(in: s, options: [], range: NSRange(s.startIndex..., in: s)) != nil
    }

    static func firstGroup(_ regex: NSRegularExpression, in s: String) -> String? {
        guard let m = regex.firstMatch(in: s, options: [],
                                       range: NSRange(s.startIndex..., in: s)),
              m.numberOfRanges > 1,
              let range = Range(m.range(at: 1), in: s)
        else { return nil }
        return String(s[range])
    }
}
