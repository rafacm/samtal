/**
 * The assertion vocabulary both consumer fixtures are written in.
 *
 * Every probe in this spike is a compile-time claim: there is no server
 * to call, so `npx tsc --noEmit` is the whole test run. Two kinds of
 * claim are worth stating, and they need opposite machinery.
 *
 * A claim that something IS allowed is an ordinary annotated value: if
 * the generated type refuses it, the file does not compile. A claim that
 * something is NOT allowed needs `@ts-expect-error` on the offending
 * line, which fails the run when the error it expects does not happen.
 * That is the direction that catches a client which types everything as
 * `any` and calls it a day.
 *
 * These helpers exist so a probe reads as its claim rather than as the
 * conditional type that implements it.
 */

/** True only when `A` and `B` are the same type, invariantly. */
export type Equals<A, B> =
  (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2
    ? true
    : false;

/** Compiles only when its argument is `true`. */
export type Expect<T extends true> = T;

/** The empty object type, spelled so it reads as what it is. */
type Nothing = Record<never, never>;

/** True when key `K` of `T` may be left out entirely. */
export type Optional<T, K extends keyof T> =
  Nothing extends Pick<T, K> ? true : false;

/** True when key `K` of `T` accepts `null` as a value. */
export type Nullable<T, K extends keyof T> = null extends T[K] ? true : false;

/**
 * Records a value as an instance of a type without running anything.
 *
 * The annotation is the assertion. The returned value is discarded, and
 * the export keeps a fixture that only ever declares things from being
 * pruned as unused.
 */
export const holds = <T,>(value: T): T => value;
