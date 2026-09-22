// engine.ts — timing utilities for the splash overlay.
// Ported from acqua-riva-design-system/project/splash/commercial/animations.jsx,
// stripped down to just the math (no Stage, no PlaybackBar, no TextSprite).

export const Easing = {
  linear:         (t: number) => t,
  easeInQuad:     (t: number) => t * t,
  easeOutQuad:    (t: number) => t * (2 - t),
  easeInOutQuad:  (t: number) => (t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t),
  easeInCubic:    (t: number) => t * t * t,
  easeOutCubic:   (t: number) => --t * t * t + 1,
  easeInOutCubic: (t: number) => (t < 0.5 ? 4 * t * t * t : (t - 1) * (2 * t - 2) * (2 * t - 2) + 1),
  easeInQuart:    (t: number) => t * t * t * t,
  easeOutQuart:   (t: number) => 1 - --t * t * t * t,
  easeInOutQuart: (t: number) => (t < 0.5 ? 8 * t * t * t * t : 1 - 8 * --t * t * t * t),
};

export type EaseFn = (t: number) => number;

export const clamp = (v: number, min: number, max: number): number =>
  Math.max(min, Math.min(max, v));

export const lerp = (a: number, b: number, t: number): number =>
  a + (b - a) * t;

type AnimateArgs = {
  from?: number;
  to?: number;
  start?: number;
  end?: number;
  ease?: EaseFn;
};

export function animate({
  from = 0,
  to = 1,
  start = 0,
  end = 1,
  ease = Easing.easeInOutCubic,
}: AnimateArgs): (t: number) => number {
  return (t: number) => {
    if (t <= start) return from;
    if (t >= end) return to;
    const local = (t - start) / (end - start);
    return from + (to - from) * ease(local);
  };
}
