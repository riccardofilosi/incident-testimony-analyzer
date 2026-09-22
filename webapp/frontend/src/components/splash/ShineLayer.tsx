import { lerp } from './engine';
import { WORDMARK_STYLE } from './Wordmark';

type Props = {
  progress: number;
  opacity?: number;
};

// Bright sweep that travels L→R across the wordmark glyphs. Plain text +
// background-clip:text clips the moving gradient to the letterforms. On paper
// the peak mixes accent + paper-white so the sweep reads without overwhelming
// the editorial ink.
export function ShineLayer({ progress, opacity = 1 }: Props) {
  const gradX = lerp(-25, 125, progress);
  const bg =
    `linear-gradient(100deg,` +
    ` rgba(26,35,48,1) ${gradX - 30}%,` +
    ` rgba(29,126,155,0.85) ${gradX - 10}%,` +
    ` rgba(245,239,225,1) ${gradX}%,` +
    ` rgba(29,126,155,0.85) ${gradX + 10}%,` +
    ` rgba(26,35,48,1) ${gradX + 30}%)`;

  return (
    <div style={{
      ...WORDMARK_STYLE,
      transform: 'translate(-50%, -50%)',
      pointerEvents: 'none',
      backgroundImage: bg,
      backgroundClip: 'text',
      WebkitBackgroundClip: 'text',
      WebkitTextFillColor: 'transparent',
      color: 'transparent',
      opacity,
    }}>
      Acqua{' '}Riva
    </div>
  );
}
