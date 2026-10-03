import { Info } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export function InfoLabel({ children, tip, className = "" }) {
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-mono uppercase tracking-[0.1em] text-muted-foreground ${className}`}>
      {children}
      <Tooltip>
        <TooltipTrigger asChild>
          <button type="button" className="opacity-50 hover:opacity-100 transition-opacity" aria-label="What's this?">
            <Info className="h-3 w-3" />
          </button>
        </TooltipTrigger>
        <TooltipContent className="max-w-[220px] text-xs font-sans normal-case tracking-normal leading-relaxed">
          {tip}
        </TooltipContent>
      </Tooltip>
    </span>
  );
}
