import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronsUpDown } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

export function SymbolCombobox({ value, onChange }) {
  const [open, setOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ["symbols"],
    queryFn: async () => (await api.get("/market/symbols")).data,
    staleTime: 3600000,
  });
  const symbols = data?.symbols || ["BTCUSDT"];

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          className="h-8 w-full justify-between rounded-sm text-xs font-mono mt-0.5"
          data-testid="symbol-combobox"
        >
          {value || "Select pair"}
          <ChevronsUpDown className="h-3.5 w-3.5 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[260px] p-0 rounded-sm" align="start">
        <Command>
          <CommandInput placeholder="Search coin…" className="h-9 text-xs font-mono" data-testid="symbol-search" />
          <CommandList>
            <CommandEmpty>No pair found.</CommandEmpty>
            <CommandGroup>
              {symbols.map((s) => (
                <CommandItem
                  key={s}
                  value={s}
                  onSelect={() => {
                    onChange(s);
                    setOpen(false);
                  }}
                  className="text-xs font-mono"
                  data-testid={`symbol-option-${s}`}
                >
                  <Check className={cn("mr-2 h-3.5 w-3.5", value === s ? "opacity-100" : "opacity-0")} />
                  {s.replace("USDT", "")} <span className="text-muted-foreground ml-1">/ USDT</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
