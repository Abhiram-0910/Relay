/** Trailing-edge debounce: `fn` runs once, `ms` after the last call, with the last args. `.cancel()`
 * drops a pending run — used on input change and unmount so a stale model-fetch never lands. */
export function debounce<A extends unknown[]>(fn: (...args: A) => void, ms: number) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const debounced = (...args: A): void => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
  debounced.cancel = (): void => {
    if (timer) clearTimeout(timer);
    timer = undefined;
  };
  return debounced;
}
