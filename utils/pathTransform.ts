/**
 * Utility functions to transform preference paths containing special characters
 * into URL-safe versions and back. This is needed to work around restrictive
 * gateways that block certain characters in URLs.
 */

// Map of unsafe characters to their safe replacements
const CHAR_MAP: Record<string, string> = {
    '<': '__LT__',
    '>': '__GT__',
    '"': '__QUOT__',
    "'": '__APOS__',
    '&': '__AMP__',
    '#': '__HASH__',
    '%': '__PCT__',
    '?': '__QUEST__',
    '=': '__EQ__',
};

// Reverse map for decoding
const REVERSE_CHAR_MAP: Record<string, string> = Object.entries(CHAR_MAP).reduce(
    (acc, [unsafe, safe]) => ({ ...acc, [safe]: unsafe }),
    {}
);

/**
 * Transforms a prefs path to be URL-safe by replacing special characters.
 */
export function encodePrefsPath(path: string): string {
    if (!path) return path;

    let encoded = path;
    // Replace each unsafe character with its safe equivalent
    Object.entries(CHAR_MAP).forEach(([unsafe, safe]) => {
        encoded = encoded.split(unsafe).join(safe);
    });

    return encoded;
}

/**
 * Reverses the URL-safe transformation to get the original prefs path.
 */
export function decodePrefsPath(encodedPath: string): string {
    if (!encodedPath) return encodedPath;

    let decoded = encodedPath;
    // Replace each safe placeholder with its original character
    // Important: Do this in reverse order of encoding to avoid conflicts
    Object.entries(REVERSE_CHAR_MAP).forEach(([safe, unsafe]) => {
        decoded = decoded.split(safe).join(unsafe);
    });

    return decoded;
}

/**
 * Checks if a path needs transformation
 * @param path The path to check
 * @returns True if the path contains characters that need transformation
 */
export function needsPathTransform(path: string): boolean {
    if (!path) return false;
    return Object.keys(CHAR_MAP).some((char) => path.includes(char));
}
