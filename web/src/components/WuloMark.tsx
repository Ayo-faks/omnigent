import { forwardRef, type ImgHTMLAttributes } from "react";
import wuloMark from "@/assets/wulo-mark-black.png";
import { cn } from "@/lib/utils";

export const WuloMark = forwardRef<HTMLImageElement, ImgHTMLAttributes<HTMLImageElement>>(
  function WuloMark({ className, alt = "wulo-work", ...props }, ref) {
    return (
      <img
        ref={ref}
        src={wuloMark}
        alt={alt}
        className={cn("object-contain dark:invert", className)}
        {...props}
      />
    );
  },
);
