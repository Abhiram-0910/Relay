// Minimal dependency-free Python tokenizer for syntax highlighting.
// ponytail: a ~30-line tokenizer instead of pulling in shiki/prism (no new dep). Covers the
// generated-client subset (strings, comments, numbers, keywords, def/class names); swap for a
// real grammar lib if richer highlighting is ever needed.
//
// Colors are deliberately COOL (violet/sky/cyan/slate) so they can't be confused with the
// status-semantic emerald/amber/red used elsewhere in the app.

export type TokenType = "str" | "com" | "kw" | "num" | "def" | "txt";
export interface Token {
  t: TokenType;
  v: string;
}

const KEYWORDS = new Set([
  "def", "class", "return", "import", "from", "as", "if", "elif", "else", "for", "while", "in",
  "not", "and", "or", "is", "None", "True", "False", "try", "except", "finally", "raise", "with",
  "lambda", "yield", "assert", "pass", "break", "continue", "global", "nonlocal", "del", "async",
  "await", "self",
]);

const TOKEN_RE =
  /("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(#[^\n]*)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)|(\s+|[^\s\w])/g;

export function tokenizePython(code: string): Token[] {
  const tokens: Token[] = [];
  let expectName = false; // the identifier right after def/class is a definition name
  let m: RegExpExecArray | null;
  TOKEN_RE.lastIndex = 0;
  while ((m = TOKEN_RE.exec(code)) !== null) {
    if (m[1]) {
      tokens.push({ t: "str", v: m[1] });
      expectName = false;
    } else if (m[2]) {
      tokens.push({ t: "com", v: m[2] });
    } else if (m[3]) {
      tokens.push({ t: "num", v: m[3] });
      expectName = false;
    } else if (m[4]) {
      const w = m[4];
      if (expectName) {
        tokens.push({ t: "def", v: w });
        expectName = false;
      } else if (KEYWORDS.has(w)) {
        tokens.push({ t: "kw", v: w });
        expectName = w === "def" || w === "class";
      } else {
        tokens.push({ t: "txt", v: w });
      }
    } else {
      tokens.push({ t: "txt", v: m[5] ?? m[0] });
    }
  }
  return tokens;
}

export const TOKEN_CLASS: Record<TokenType, string> = {
  str: "text-sky-300",
  com: "text-slate-500 italic",
  kw: "text-violet-300",
  num: "text-cyan-300",
  def: "text-violet-200",
  txt: "text-slate-200",
};
