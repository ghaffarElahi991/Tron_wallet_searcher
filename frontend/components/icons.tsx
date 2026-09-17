import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function IconBase({ children, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

export const SparkIcon = (props: IconProps) => (
  <IconBase {...props}><path d="m12 3 1.15 3.85L17 8l-3.85 1.15L12 13l-1.15-3.85L7 8l3.85-1.15L12 3Z"/><path d="m18.5 14 .7 2.3 2.3.7-2.3.7-.7 2.3-.7-2.3-2.3-.7 2.3-.7.7-2.3Z"/><path d="m5 14 .85 2.15L8 17l-2.15.85L5 20l-.85-2.15L2 17l2.15-.85L5 14Z"/></IconBase>
);

export const GridIcon = (props: IconProps) => (
  <IconBase {...props}><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></IconBase>
);

export const OrdersIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M6 3h12v18H6z"/><path d="M9 7h6M9 11h6M9 15h4"/></IconBase>
);

export const ShieldIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M12 3 4.5 6v5.2c0 4.7 3.1 8.2 7.5 9.8 4.4-1.6 7.5-5.1 7.5-9.8V6L12 3Z"/><path d="m9 12 2 2 4-4"/></IconBase>
);

export const HelpIcon = (props: IconProps) => (
  <IconBase {...props}><circle cx="12" cy="12" r="9"/><path d="M9.7 9a2.4 2.4 0 1 1 3.7 2c-.9.6-1.4 1-1.4 2M12 17h.01"/></IconBase>
);

export const WalletIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M4 6.5h14A2.5 2.5 0 0 1 20.5 9v9A2.5 2.5 0 0 1 18 20.5H5A2.5 2.5 0 0 1 2.5 18V6A2.5 2.5 0 0 1 5 3.5h12"/><path d="M15.5 11.5h5v5h-5a2.5 2.5 0 0 1 0-5Z"/><circle cx="16" cy="14" r=".4" fill="currentColor" stroke="none"/></IconBase>
);

export const ArrowIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M5 12h14M14 7l5 5-5 5"/></IconBase>
);

export const CheckIcon = (props: IconProps) => (
  <IconBase {...props}><path d="m5 12 4 4L19 6"/></IconBase>
);

export const ChevronIcon = (props: IconProps) => (
  <IconBase {...props}><path d="m9 18 6-6-6-6"/></IconBase>
);

export const CopyIcon = (props: IconProps) => (
  <IconBase {...props}><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></IconBase>
);

export const LockIcon = (props: IconProps) => (
  <IconBase {...props}><rect x="4" y="10" width="16" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/></IconBase>
);

export const TelegramIcon = (props: IconProps) => (
  <IconBase {...props}><path d="m21 4-3 16-6-4-3 3v-5l8-7-10 6-4-2 18-7Z"/></IconBase>
);

export const ActivityIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M3 12h4l2-7 4 14 2-7h6"/></IconBase>
);

export const ExternalIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M14 4h6v6M20 4l-9 9"/><path d="M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6"/></IconBase>
);

export const KeyIcon = (props: IconProps) => (
  <IconBase {...props}><circle cx="8" cy="15" r="4"/><path d="m11 12 8-8M15 8l2 2M17 6l2 2"/></IconBase>
);

export const ClockIcon = (props: IconProps) => (
  <IconBase {...props}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></IconBase>
);

export const AlertIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M12 4 2.8 20h18.4L12 4Z"/><path d="M12 9v5M12 17h.01"/></IconBase>
);
