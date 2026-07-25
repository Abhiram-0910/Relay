import { describe, expect, it } from "vitest";

import { tokenizePython } from "./highlight";

describe("tokenizePython", () => {
  it("classifies keywords, def names, strings, and comments", () => {
    const toks = tokenizePython('def foo():\n    x = "hi"  # note\n    return None');
    const of = (t: string) => toks.filter((k) => k.t === t).map((k) => k.v);
    expect(of("kw")).toEqual(expect.arrayContaining(["def", "return", "None"]));
    expect(of("def")).toContain("foo"); // identifier right after def
    expect(of("str")).toContain('"hi"');
    expect(of("com").join("")).toContain("# note");
  });

  it("round-trips — concatenated tokens equal the input (no chars dropped or altered)", () => {
    const src = 'from x import y\nclass A(B):\n    """doc"""\n    n = 42\n    pass\n';
    expect(
      tokenizePython(src)
        .map((t) => t.v)
        .join(""),
    ).toBe(src);
  });
});
