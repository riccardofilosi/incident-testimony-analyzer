import { useLayoutEffect, useRef, useState } from 'react';
import { animate, clamp, Easing, lerp } from './engine';
import { DropDot } from './DropDot';
import { Wordmark } from './Wordmark';
import { ShineLayer } from './ShineLayer';

type Props = {
  time: number;
  flyTransform?: string;
  outroOpacity?: number;
};

// Scene fills the viewport. The wordmark + drop live inside a "fly group" that
// can be translated/scaled by the parent overlay during PHASE 5 (fly-to-crest).
// Everything else (backdrop, tagline, shine pulse) is outside the fly group so
// it stays fixed / fades on its own.
export function Scene({ time: t, flyTransform, outroOpacity = 1 }: Props) {
  const dotRef = useRef<HTMLSpanElement | null>(null);
  const wordmarkRef = useRef<HTMLDivElement | null>(null);

  const [target, setTarget] = useState({
    x: typeof window !== 'undefined' ? window.innerWidth * 0.612 : 1175,
    y: typeof window !== 'undefined' ? window.innerHeight * 0.46 : 497,
    size: 22,
  });
  const [wordBox, setWordBox] = useState({ left: 0, width: 600 });

  useLayoutEffect(() => {
    const measure = () => {
      if (!dotRef.current || !wordmarkRef.current) return;
      const dr = dotRef.current.getBoundingClientRect();
      const wr = wordmarkRef.current.getBoundingClientRect();
      if (dr.width <= 0) return;
      setTarget({
        x: dr.left + dr.width / 2,
        y: dr.top + dr.height / 2,
        size: dr.width,
      });
      setWordBox({ left: wr.left, width: wr.width });
    };
    measure();
    const onResize = () => measure();
    window.addEventListener('resize', onResize);
    if (document.fonts && document.fonts.ready) {
      void document.fonts.ready.then(measure);
    }
    const ids = [
      window.setTimeout(measure, 200),
      window.setTimeout(measure, 800),
      window.setTimeout(measure, 2000),
    ];
    return () => {
      window.removeEventListener('resize', onResize);
      ids.forEach(clearTimeout);
    };
  }, []);

  // ── Phase timings (identical to standalone scene, all within 4.2s) ─────────
  const fallP = animate({ from: 0, to: 1, start: 0.10, end: 2.20, ease: Easing.easeInOutQuart })(t);
  const dropY = lerp(-180, target.y, fallP);
  const dropX = target.x;

  const morph = animate({ from: 0, to: 1, start: 1.85, end: 2.45, ease: Easing.easeInOutCubic })(t);
  const textP = animate({ from: 0, to: 1, start: 0.55, end: 2.30, ease: Easing.easeOutCubic })(t);
  const dropOpacity = animate({ from: 0, to: 1, start: 0, end: 0.25, ease: Easing.easeOutCubic })(t);

  // Shine sweep
  const SHINE_START = 2.65;
  const SHINE_END = 3.65;
  const shineP = animate({ from: 0, to: 1, start: SHINE_START, end: SHINE_END, ease: Easing.easeInOutCubic })(t);
  const shineOpacity = (() => {
    if (t < SHINE_START - 0.05) return 0;
    if (t > SHINE_END + 0.1) return 0;
    if (t < SHINE_START + 0.15) return clamp((t - (SHINE_START - 0.05)) / 0.2, 0, 1);
    if (t > SHINE_END - 0.15) return clamp((SHINE_END + 0.1 - t) / 0.25, 0, 1);
    return 1;
  })();

  // Tagline shows mid-shine, fades out before the fly starts
  const taglineIn = animate({ from: 0, to: 1, start: 2.80, end: 3.40, ease: Easing.easeOutCubic })(t);
  const taglineOut = animate({ from: 0, to: 1, start: 4.20, end: 4.50, ease: Easing.easeInOutCubic })(t);
  const taglineOpacity = taglineIn * (1 - taglineOut);

  const dotXFraction = wordBox.width > 0 ? clamp((target.x - wordBox.left) / wordBox.width, 0, 1) : 0.62;
  const sweepPct = lerp(-25, 125, shineP);
  const dotPct = dotXFraction * 100;
  const dotShineBoost = shineOpacity > 0 ? Math.max(0, 1 - Math.abs(sweepPct - dotPct) / 22) : 0;

  return (
    <div style={{ position: 'absolute', inset: 0, opacity: outroOpacity }}>
      {/* Backdrop is rendered by SplashOverlay (it has the radial reveal mask).
          Scene now contains only the foreground elements. */}

      {/* Fly group — gets translated/scaled toward the crest during PHASE 5.
          Wordmark + DropDot live here so they move as a unit. */}
      <div style={{
        position: 'absolute', inset: 0,
        transform: flyTransform ?? 'none',
        transformOrigin: '50% 50%',
        willChange: 'transform',
      }}>
        {/* Layout ghost — invisible, used only to measure the natural i-dot position */}
        <Wordmark layoutOnly={true} dotRef={dotRef} wordmarkRef={wordmarkRef} />

        {/* Visible wordmark */}
        <Wordmark progress={textP} />

        {/* Drop morphing into the dot */}
        <DropDot
          x={dropX} y={dropY}
          dotPxSize={target.size}
          morph={morph}
          opacity={dropOpacity}
        />
      </div>

      {/* Shine sweep — independent of fly, fades out before fly starts */}
      {shineOpacity > 0 && (
        <ShineLayer progress={shineP} opacity={shineOpacity} />
      )}

      {/* Dot shine pulse — teal multiply on paper */}
      {dotShineBoost > 0 && (
        <div style={{
          position: 'absolute',
          left: target.x, top: target.y,
          width: target.size * 2.2, height: target.size * 2.2,
          transform: 'translate(-50%, -50%)',
          borderRadius: '50%',
          background: `radial-gradient(circle, rgba(29,126,155,${0.75 * dotShineBoost}) 0%, rgba(29,126,155,${0.3 * dotShineBoost}) 35%, rgba(29,126,155,0) 70%)`,
          mixBlendMode: 'multiply',
          pointerEvents: 'none',
          filter: 'blur(0.4px)',
        }} />
      )}

      {/* Tagline — italic Newsreader, fades in mid-shine, out before fly */}
      <div style={{
        position: 'absolute',
        left: '50%', top: '50%',
        transform: 'translate(-50%, calc(-50% + 130px))',
        fontFamily: '"Newsreader", Georgia, serif',
        fontStyle: 'italic',
        fontWeight: 400,
        fontSize: 22,
        color: 'var(--ink-soft)',
        letterSpacing: '0.01em',
        lineHeight: 1.4,
        whiteSpace: 'nowrap',
        opacity: taglineOpacity,
        pointerEvents: 'none',
      }}>
        Analisi automatica delle testimonianze sugli eventi critici di stabilimento.
      </div>
    </div>
  );
}
