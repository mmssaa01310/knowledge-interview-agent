import type { ImgHTMLAttributes } from "react";
import { useTheme } from "../../theme";

type ThemeLogoProps = Omit<ImgHTMLAttributes<HTMLImageElement>, "src">;

export function ThemeLogo({ alt = "KIKIORI", ...props }: ThemeLogoProps) {
  const { theme } = useTheme();
  const source = theme === "dark" ? "/images/kikiori-logo-dark.svg" : "/images/kikiori-logo-light.svg";

  return <img {...props} src={source} alt={alt} />;
}
