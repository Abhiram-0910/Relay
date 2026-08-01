import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { debounce } from "./debounce";

describe("debounce — the model-fetch fires once after typing settles, not per keystroke", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("collapses rapid calls into a single trailing invocation with the last args", () => {
    const fn = vi.fn();
    const d = debounce(fn, 500);

    d("a");
    d("ab");
    d("abc"); // three keystrokes in quick succession
    expect(fn).not.toHaveBeenCalled(); // nothing has fired yet

    vi.advanceTimersByTime(499);
    expect(fn).not.toHaveBeenCalled(); // still within the window

    vi.advanceTimersByTime(1);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith("abc"); // only the final value
  });

  it("cancel() drops a pending invocation (stale fetch never lands)", () => {
    const fn = vi.fn();
    const d = debounce(fn, 500);

    d("x");
    d.cancel();
    vi.advanceTimersByTime(1000);
    expect(fn).not.toHaveBeenCalled();
  });
});
