export type KikoAvatarState = "waiting" | "thinking" | "error";

const avatarSources: Record<KikoAvatarState, string> = {
  waiting: "/images/kiko-waiting.svg",
  thinking: "/images/kiko-thinking.svg",
  error: "/images/kiko-error.svg",
};

type KikoAvatarProps = {
  state: KikoAvatarState;
  label: string;
};

export function KikoAvatar({ state, label }: KikoAvatarProps) {
  return (
    <span className={`kiko-avatar ${state}`} role="img" aria-label={label}>
      <img src={avatarSources[state]} alt="" aria-hidden="true" />
    </span>
  );
}
