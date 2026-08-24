interface TPMarkProps {
  compact?: boolean;
}

export function TPMark({ compact = false }: TPMarkProps) {
  return (
    <span className={compact ? "tp-mark tp-mark--compact" : "tp-mark"} aria-hidden>
      <svg viewBox="0 0 48 48" role="img">
        <path className="tp-mark-frame" d="M11 7.5h26a3.5 3.5 0 0 1 3.5 3.5v26a3.5 3.5 0 0 1-3.5 3.5H11A3.5 3.5 0 0 1 7.5 37V11A3.5 3.5 0 0 1 11 7.5Z" />
        <path className="tp-mark-glyph" d="M13 15.5h21M23.5 15.5v18M23.5 22.5h7.2a5.5 5.5 0 0 1 0 11h-2.1" />
        <circle className="tp-mark-node" cx="37" cy="15.5" r="2.2" />
      </svg>
    </span>
  );
}
