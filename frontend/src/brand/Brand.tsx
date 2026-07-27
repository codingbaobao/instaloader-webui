export const PRODUCT_NAME = "Instaloader WebUI";

type BrandMarkProps = Readonly<{
  className?: string;
  label?: string;
}>;

type BrandLockupProps = Readonly<{
  className?: string;
}>;

export function BrandMark({ className, label }: BrandMarkProps) {
  return (
    <img
      alt={label ?? ""}
      aria-hidden={label === undefined ? true : undefined}
      className={className}
      src="/brand/instaloader-webui.svg"
    />
  );
}

export function BrandLockup({ className }: BrandLockupProps) {
  const classes = ["brand-lockup", className].filter(Boolean).join(" ");

  return (
    <span aria-label={PRODUCT_NAME} className={classes} role="img">
      <BrandMark className="brand-lockup-mark" />
      <span aria-hidden="true" className="brand-lockup-name">
        Instaloader
      </span>
      <span aria-hidden="true" className="brand-lockup-suffix">
        WebUI
      </span>
    </span>
  );
}
