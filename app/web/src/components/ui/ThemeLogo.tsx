import type { HTMLAttributes } from "react";

type ThemeLogoProps = Omit<HTMLAttributes<HTMLSpanElement>, "role" | "aria-label"> & {
  alt?: string;
  compact?: boolean;
};

export function ThemeLogo({ alt = "MUSUBI", compact = false, className, ...props }: ThemeLogoProps) {
  const classes = ["brand-lockup", className, compact ? "compact" : ""].filter(Boolean).join(" ");

  return (
    <span {...props} className={classes} role="img" aria-label={alt}>
      <span className="brand-symbol" aria-hidden="true">
        <svg viewBox="0 0 36 36" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M5 13c5.3 0 5.3 10 10.6 10s5.3-10 10.6-10S31.5 23 31.5 23" />
          <path d="M5 23c5.3 0 5.3-10 10.6-10s5.3 10 10.6 10S31.5 13 31.5 13" />
        </svg>
      </span>
      <span className="brand-wordmark" aria-hidden="true">MUSUBI</span>
    </span>
  );
}
