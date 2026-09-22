import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Scene } from './Scene';
import { animate, clamp, Easing, lerp } from './engine';
import { WORDMARK_FONT_SIZE } from './Wordmark';

// Timeline
// 0.0 → 4.2  Scene (drop falls, morphs into i, shine sweep, tagline)
// 4.2 → 4.5  Tagline fade out (handled inside Scene)
// 4.5 → 5.5  PHASE 5 — fly-to-crest
// 5.5 → 6.7  PHASE 6 — radial reveal: dashboard emerges from the wordmark point
// 6.7        onComplete()
const DURATION = 6.7;
const FLY_START = 4.5;
const FLY_END = 5.5;
const REVEAL_END = 6.7;
const SAFETY_MS = 9000;

type Props = {
  onComplete: () => void;
};

type Anchor = { left: number; top: number; width: number; height: number };

// Hardcoded fallback geometry if the crest anchor isn't measurable yet
const ANCHOR_FALLBACK: Anchor = { left: 22, top: 32, width: 90, height: 20 };

export function SplashOverlay({ onComplete }: Props) {
  const reducedMotion =
    typeof window !== 'undefined' &&
    window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const [time, setTime] = useState(reducedMotion ? DURATION : 0);
  const [anchor, setAnchor] = useState<Anchor>(ANCHOR_FALLBACK);
  const [skipped, setSkipped] = useState(false);
  const completedRef = useRef(false);
  const rafRef = useRef<number | null>(null);
  const lastTsRef = useRef<number | null>(null);

  // ── Measure the actual "Acqua Riva" anchor span in the live crest ─────────
  useLayoutEffect(() => {
    const measure = () => {
      const el = document.querySelector('.crest-wordmark-anchor');
      if (!el) return;
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {
        setAnchor({ left: r.left, top: r.top, width: r.width, height: r.height });
      }
    };
    measure();
    const onResize = () => measure();
    window.addEventListener('resize', onResize);
    if (document.fonts && document.fonts.ready) void document.fonts.ready.then(measure);
    const ids = [
      window.setTimeout(measure, 100),
      window.setTimeout(measure, 400),
      window.setTimeout(measure, 1200),
    ];
    return () => {
      window.removeEventListener('resize', onResize);
      ids.forEach(clearTimeout);
    };
  }, []);

  // ── Playhead via requestAnimationFrame ────────────────────────────────────
  useEffect(() => {
    if (reducedMotion) return;
    const step = (ts: number) => {
      if (lastTsRef.current == null) lastTsRef.current = ts;
      const dt = (ts - lastTsRef.current) / 1000;
      lastTsRef.current = ts;
      setTime((t) => Math.min(DURATION, t + dt));
      rafRef.current = requestAnimationFrame(step);
    };
    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      lastTsRef.current = null;
    };
  }, [reducedMotion]);

  // ── Safety net ────────────────────────────────────────────────────────────
  useEffect(() => {
    const id = window.setTimeout(() => {
      if (!completedRef.current) {
        completedRef.current = true;
        onComplete();
      }
    }, SAFETY_MS);
    return () => clearTimeout(id);
  }, [onComplete]);

  // ── Skip on Esc ───────────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setSkipped(true); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // ── Fire onComplete when we reach REVEAL_END (or skip / reduced motion) ──
  useEffect(() => {
    if (completedRef.current) return;
    if (skipped || reducedMotion || time >= REVEAL_END) {
      completedRef.current = true;
      onComplete();
    }
  }, [time, skipped, reducedMotion, onComplete]);

  // ── PHASE 5: fly-to-crest ─────────────────────────────────────────────────
  const flyP = animate({ from: 0, to: 1, start: FLY_START, end: FLY_END, ease: Easing.easeInOutCubic })(time);
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1920;
  const vh = typeof window !== 'undefined' ? window.innerHeight : 1080;
  // Scale ratio = target text height / source text height. Anchor span height
  // ≈ font-size × line-height, so this lands pixel-perfect when the anchor is
  // sized exactly like the splash wordmark glyphs.
  const targetScale = anchor.height / WORDMARK_FONT_SIZE;
  const anchorCenterX = anchor.left + anchor.width / 2;
  const anchorCenterY = anchor.top + anchor.height / 2;
  const dxFinal = anchorCenterX - vw / 2;
  const dyFinal = anchorCenterY - vh / 2;
  const dx = lerp(0, dxFinal, flyP);
  const dy = lerp(0, dyFinal, flyP);
  const scale = lerp(1, targetScale, flyP);
  const flyTransform = flyP > 0
    ? `translate(${dx}px, ${dy}px) scale(${scale})`
    : undefined;

  // ── PHASE 6: radial reveal ────────────────────────────────────────────────
  // The backdrop hides the dashboard while splash plays. As soon as the
  // wordmark has landed, a transparent circle grows from the anchor point —
  // the dashboard emerges outward as if generated by the wordmark itself.
  const revealP = animate({ from: 0, to: 1, start: FLY_END, end: REVEAL_END, ease: Easing.easeInOutCubic })(time);
  // Max radius needed to cover the viewport from the anchor (worst case: the
  // farthest viewport corner). Add some padding so the wipe finishes off-screen.
  const farthestCorner = (() => {
    const dxL = Math.max(anchorCenterX, vw - anchorCenterX);
    const dyL = Math.max(anchorCenterY, vh - anchorCenterY);
    return Math.sqrt(dxL * dxL + dyL * dyL) + 80;
  })();
  const revealRadius = lerp(0, farthestCorner, revealP);
  // Soft 40px edge for a more elegant wipe
  const maskInner = Math.max(0, revealRadius - 20);
  const maskOuter = revealRadius + 20;

  // Skip / reduced-motion immediate fade
  const instantFade = skipped || reducedMotion;
  const backdropMask = instantFade
    ? undefined
    : `radial-gradient(circle at ${anchorCenterX}px ${anchorCenterY}px,` +
      ` rgba(0,0,0,0) 0px,` +
      ` rgba(0,0,0,0) ${maskInner}px,` +
      ` rgba(0,0,0,1) ${maskOuter}px,` +
      ` rgba(0,0,0,1) 100%)`;

  // Overall overlay opacity: 1 during scene + fly, instant 0 on skip
  const overlayOpacity = instantFade ? 0 : 1;
  // The splash wordmark stays visible until the overlay unmounts at REVEAL_END,
  // at which point the real .crest-title underneath becomes visible. With
  // pixel-perfect alignment the handoff is seamless.

  return (
    <div
      aria-hidden="true"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 9999,
        pointerEvents: revealP > 0.9 ? 'none' : 'auto',
        opacity: overlayOpacity,
        transition: instantFade ? 'opacity 200ms ease-out' : undefined,
      }}>
      {/* Backdrop layer — paper + glow, with radial mask during reveal */}
      <div style={{
        position: 'absolute', inset: 0,
        background: 'var(--paper)',
        overflow: 'hidden',
        maskImage: backdropMask,
        WebkitMaskImage: backdropMask,
        willChange: 'mask-image',
      }}>
        <div style={{
          position: 'absolute', inset: 0,
          background: 'radial-gradient(ellipse 1100px 760px at 50% 50%, rgba(29,126,155,0.05) 0%, rgba(245,239,225,0) 65%)',
        }} />
      </div>

      {/* Scene (wordmark + drop + tagline + shine) — sits above the backdrop.
          It is NOT under the radial mask so the wordmark stays visible at the
          crest position through the entire reveal phase. */}
      <Scene
        time={time}
        flyTransform={flyTransform}
        outroOpacity={1}
      />


      {/* Skip button — disappears as reveal completes */}
      <button
        onClick={() => setSkipped(true)}
        style={{
          position: 'absolute',
          top: 28, right: 32, zIndex: 10,
          background: 'transparent',
          border: '1px solid var(--ink)',
          color: 'var(--ink)',
          padding: '7px 14px',
          fontFamily: '"DM Sans", system-ui, sans-serif',
          fontSize: 11, letterSpacing: '0.18em',
          textTransform: 'uppercase', fontWeight: 600,
          cursor: 'pointer',
          opacity: clamp(0.55 - revealP * 0.8, 0, 0.55),
        }}>
        salta intro →
      </button>
    </div>
  );
}
