import Foundation

/// A normalised handle for one channel of one person: an email address or a
/// phone number. Comparing raw strings does not work — the same person appears
/// as `Anna@Lanificio.IT`, `anna@lanificio.it`, `+39 055 1234567` and
/// `0039 055 1234567` across the six stores.
public struct Identity: Hashable, Comparable, CustomStringConvertible {
    public enum Kind: String { case email = "mailto", phone = "tel" }

    public let kind: Kind
    public let value: String

    public var raw: String { "\(kind.rawValue):\(value)" }
    public var display: String { value }
    public var description: String { raw }

    public static func < (a: Identity, b: Identity) -> Bool { a.raw < b.raw }

    private init(kind: Kind, value: String) {
        self.kind = kind
        self.value = value
    }

    /// Canonical key for an email address, or nil if the input is not one.
    public static func email(_ raw: String?) -> Identity? {
        guard var s = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else {
            return nil
        }
        // `Name <addr@host>` and `"Name" <addr@host>` -> `addr@host`. This has to
        // happen before any angle-bracket stripping, or the closing bracket is
        // removed first and the extraction can no longer match.
        if let open = s.firstIndex(of: "<"), let close = s.lastIndex(of: ">"), open < close {
            s = String(s[s.index(after: open)..<close])
        }
        s = s.trimmingCharacters(in: CharacterSet(charactersIn: "<> \t\r\n"))
             .lowercased()
        let parts = s.split(separator: "@", omittingEmptySubsequences: false)
        guard parts.count == 2,
              !parts[0].isEmpty,
              parts[1].contains("."),
              !parts[1].hasPrefix("."),
              !parts[1].hasSuffix("."),
              !s.contains(where: { $0.isWhitespace })
        else { return nil }
        return Identity(kind: .email, value: s)
    }

    /// Canonical key for a phone number.
    ///
    /// Keyed on the last nine significant digits rather than a fully qualified
    /// E.164 number. The local stores mix `+39 055 1234567`, `0039 055 1234567`,
    /// `055 1234567` and `39 055 1234567` for one person, and reconciling those
    /// properly needs a country-code library plus the user's home region. The
    /// last nine digits are stable across all of those forms and collide only
    /// rarely at address-book scale. A deliberate trade, not an oversight.
    public static func phone(_ raw: String?) -> Identity? {
        guard let raw = raw else { return nil }
        let digits = raw.filter { $0.isASCII && $0.isNumber }
        guard digits.count >= 6 else { return nil }
        return Identity(kind: .phone, value: String(digits.suffix(9)))
    }

    /// An iMessage or CallKit handle, which may be either form.
    public static func handle(_ raw: String?) -> Identity? {
        guard let raw = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty else {
            return nil
        }
        return raw.contains("@") ? email(raw) : phone(raw)
    }
}

/// Machine, role and bulk senders, which should never be ranked as people.
public enum RoleAddress {
    private static let localPart = try! NSRegularExpression(
        pattern: "^(no-?reply|do-?not-?reply|donotreply|reply|bounces?|"
            + "mailer-daemon|postmaster|abuse|hostmaster|webmaster|"
            + "notifications?|notify|alerts?|noc|"
            + "newsletters?|news|marketing|mailing|lists?|majordomo|"
            + "support|help|helpdesk|services?|contact|info|hello|hi|enquiries|"
            + "admin|administrator|root|daemon|system|automated|auto|robot|bot|"
            + "billing|invoices?|accounts?|payments?|receipts?|orders?|"
            + "security|privacy|legal|compliance|dpo|"
            + "careers|jobs|recruiting|hr|"
            + "updates?|digest|feedback|survey|events?|calendar-notification)"
            + "([+._-].*)?$",
        options: [.caseInsensitive])

    private static let domain = try! NSRegularExpression(
        pattern: "(^|\\.)(bounces?|mail|email|e?mailer|smtp|mx|reply|notifications?|alerts?|"
            + "sendgrid\\.net|amazonses\\.com|mailgun\\.org|sparkpostmail\\.com|"
            + "mcsv\\.net|mcdlv\\.net|rsgsv\\.net|createsend\\.com|cmail\\d*\\.com|"
            + "salesforce\\.com|hubspot\\.com|intercom-mail\\.com|zendesk\\.com)$",
        options: [.caseInsensitive])

    public static func matches(_ identity: Identity) -> Bool {
        guard identity.kind == .email else { return false }
        let parts = identity.value.split(separator: "@", maxSplits: 1)
        guard parts.count == 2 else { return false }
        return matches(localPart, String(parts[0])) || matches(domain, String(parts[1]))
    }

    private static func matches(_ regex: NSRegularExpression, _ s: String) -> Bool {
        regex.firstMatch(in: s, options: [], range: NSRange(s.startIndex..., in: s)) != nil
    }
}

/// Disjoint-set union over identities, used to fuse the several handles that
/// belong to one human.
public struct DisjointSet<T: Hashable> {
    private var parent: [T: T] = [:]

    public init() {}

    public mutating func find(_ x: T) -> T {
        if parent[x] == nil { parent[x] = x }
        var root = x
        while let p = parent[root], p != root { root = p }
        var cursor = x
        while let p = parent[cursor], p != root {
            parent[cursor] = root
            cursor = p
        }
        return root
    }

    public mutating func union(_ a: T, _ b: T) {
        let ra = find(a), rb = find(b)
        if ra != rb { parent[rb] = ra }
    }
}
