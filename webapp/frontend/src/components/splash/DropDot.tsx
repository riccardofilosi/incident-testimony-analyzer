import { lerp } from './engine';

// Morph path: elongated teardrop (p=0) ↔ circle (p=1). Same 4-segment bezier
// topology so it tweens cleanly. Copied from Claude Design's commercial scene
// — the morph topology was the part worth keeping verbatim.
function morphPath(p: number): string {
  const L = (a: number, b: number) => a + (b - a) * p;
  const tx = 0, ty = L(-45, -18);
  const a1 = `${L( 8,  10)} ${L(-42, -18)}`;
  const a2 = `${L(18,  18)} ${L(-22, -10)}`;
  const ae = `${L(18,  18)} ${L(  0,   0)}`;
  const b1 = `${L(18,  18)} ${L( 16,  10)}`;
  const b2 = `${L(12,  10)} ${L( 25,  18)}`;
  const be = `0 ${L( 25,  18)}`;
  const c1 = `${L(-12, -10)} ${L( 25,  18)}`;
  const c2 = `${L(-18, -18)} ${L( 16,  10)}`;
  const ce = `${L(-18, -18)} ${L(  0,   0)}`;
  const d1 = `${L(-18, -18)} ${L(-22, -10)}`;
  const d2 = `${L( -8, -10)} ${L(-42, -18)}`;
  return `M ${tx} ${ty}` +
         ` C ${a1}, ${a2}, ${ae}` +
         ` C ${b1}, ${b2}, ${be}` +
         ` C ${c1}, ${c2}, ${ce}` +
         ` C ${d1}, ${d2}, ${tx} ${ty} Z`;
}

type Props = {
  x: number;
  y: number;
  dotPxSize: number;
  morph: number;
  opacity?: number;
};

// Morph ends at 1.7× the i's natural dot — the shine pulse is then smaller
// than the dot, so the user doesn't perceive the dot "growing" during shine.
const DOT_BOOST = 1.7;

export function DropDot({ x, y, dotPxSize, morph, opacity = 1 }: Props) {
  const svgFinal = ((dotPxSize * DOT_BOOST) / 36) * 100;
  const svgStart = (62 / 70) * 100;
  const svgSize = lerp(svgStart, svgFinal, morph);
  const d = morphPath(morph);

  const flightStretch = 1 + (1 - morph) * 0.04;
  const solidIn = morph * morph;
  const glassOut = 1 - solidIn;

  return (
    <div style={{
      position: 'absolute',
      left: x, top: y,
      width: svgSize, height: svgSize,
      transform: `translate(-50%, -50%) scaleY(${flightStretch})`,
      opacity,
      pointerEvents: 'none',
      willChange: 'transform, opacity',
    }}>
      <svg width={svgSize} height={svgSize} viewBox="-50 -50 100 100" style={{ overflow: 'visible' }}>
        <defs>
          <radialGradient id="aqr-dropBody" cx="0.5" cy="0.55" r="0.6">
            <stop offset="0%"   stopColor="rgba(29,126,155,0)" />
            <stop offset="55%"  stopColor="rgba(29,126,155,0.08)" />
            <stop offset="92%"  stopColor="rgba(21,94,116,0.32)" />
            <stop offset="100%" stopColor="rgba(29,126,155,0)" />
          </radialGradient>
          <linearGradient id="aqr-dropRim" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor="rgba(29,126,155,0.6)" />
            <stop offset="35%"  stopColor="rgba(29,126,155,0)" />
            <stop offset="65%"  stopColor="rgba(29,126,155,0)" />
            <stop offset="100%" stopColor="rgba(21,94,116,0.85)" />
          </linearGradient>
          <radialGradient id="aqr-hlSharp" cx="0.5" cy="0.5" r="0.5">
            <stop offset="0%"   stopColor="rgba(255,255,255,1)" />
            <stop offset="60%"  stopColor="rgba(255,255,255,0.4)" />
            <stop offset="100%" stopColor="rgba(255,255,255,0)" />
          </radialGradient>
          <filter id="aqr-softBlur" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="0.5" />
          </filter>
          <filter id="aqr-atmGlow" x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="3.2" />
          </filter>
          <filter id="aqr-rimBlur" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="0.35" />
          </filter>
        </defs>

        <path d={d} fill="rgba(29,126,155,0.10)" filter="url(#aqr-atmGlow)" opacity={glassOut} />
        <path d={d} fill="url(#aqr-dropBody)" opacity={glassOut} />
        <path d={d} fill="none" stroke="url(#aqr-dropRim)" strokeWidth="1.1"
          opacity={glassOut} filter="url(#aqr-rimBlur)" />

        <path d="M -14 10 Q 0 26 14 10"
          fill="none" stroke="rgba(21,94,116,0.9)" strokeWidth="1.4"
          strokeLinecap="round" opacity={glassOut} />
        <path d="M -10 14 Q 0 22 10 14"
          fill="none" stroke="rgba(29,126,155,0.5)" strokeWidth="0.8"
          strokeLinecap="round" opacity={glassOut * 0.8} filter="url(#aqr-softBlur)" />

        <g opacity={glassOut}>
          <ellipse cx="-7" cy="-16" rx="2.4" ry="13"
            fill="rgba(255,255,255,0.7)" filter="url(#aqr-softBlur)" />
          <ellipse cx="-7" cy="-28" rx="1.4" ry="5"
            fill="rgba(255,255,255,0.95)" />
          <circle cx="-5" cy="-36" r="1.3" fill="url(#aqr-hlSharp)" />
          <ellipse cx="8" cy="-10" rx="1.4" ry="7"
            fill="rgba(255,255,255,0.35)" filter="url(#aqr-softBlur)" />
        </g>

        <path d={d} fill="var(--ink)" opacity={solidIn} />
      </svg>
    </div>
  );
}
