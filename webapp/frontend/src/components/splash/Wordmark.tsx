import type { CSSProperties, RefObject } from 'react';
import { lerp } from './engine';

export const WORDMARK_FONT_SIZE = 168;

export const WORDMARK_STYLE: CSSProperties = {
  position: 'absolute',
  left: '50%',
  top: '50%',
  fontFamily: '"Newsreader", Georgia, serif',
  fontWeight: 500,
  fontSize: WORDMARK_FONT_SIZE,
  letterSpacing: '-0.02em',
  color: 'var(--ink)',
  lineHeight: 1,
  whiteSpace: 'pre',
  pointerEvents: 'none',
};

type Props = {
  progress?: number;
  layoutOnly?: boolean;
  dotRef?: RefObject<HTMLSpanElement | null>;
  wordmarkRef?: RefObject<HTMLDivElement | null>;
  flyTransform?: string;
  flyTransformOrigin?: string;
};

export function Wordmark({
  progress = 1,
  layoutOnly = false,
  dotRef,
  wordmarkRef,
  flyTransform,
  flyTransformOrigin,
}: Props) {
  const revealPos = lerp(-8, 108, layoutOnly ? 1 : progress);
  const mask =
    `linear-gradient(100deg,` +
    ` rgba(0,0,0,1) 0%,` +
    ` rgba(0,0,0,1) ${revealPos - 8}%,` +
    ` rgba(0,0,0,0) ${revealPos + 8}%,` +
    ` rgba(0,0,0,0) 100%)`;

  return (
    <div
      ref={wordmarkRef}
      style={{
        ...WORDMARK_STYLE,
        opacity: layoutOnly ? 0 : 1,
        maskImage: mask,
        WebkitMaskImage: mask,
        maskMode: 'alpha',
        transform: flyTransform ?? 'translate(-50%, -50%)',
        transformOrigin: flyTransformOrigin ?? 'center center',
        willChange: 'transform, opacity',
      }}>
      Acqua{' '}R<span style={{ position: 'relative' }}>i{!layoutOnly && (
        <span style={{
          position: 'absolute',
          left: '50%',
          top: '0.06em',
          width: '0.16em',
          height: '0.16em',
          transform: 'translate(-50%, -50%)',
          background: 'var(--paper)',
          borderRadius: '50%',
          pointerEvents: 'none',
        }} />
      )}{layoutOnly && dotRef && (
        <span ref={dotRef} style={{
          position: 'absolute',
          left: '50%',
          top: '0.06em',
          width: '0.10em',
          height: '0.10em',
          transform: 'translate(-50%, -50%)',
        }} />
      )}</span>va
    </div>
  );
}
