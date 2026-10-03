import { useState, useMemo } from "react";
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

  const options = useMemo(
    () => data?.options || [{ symbol: "BTCUSDT", label: "BTC / USDT", market: "Crypto (Binance)" }],
    [data]
  );

  const groups = useMemo(() => {
    const g = {};
    for (const o of options) {
      (g[o.market] = g[o.market] || []).push(o);
    }
    return g;
  }, [options]);

  const current = options.find((o) => o.symbol === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          className="h-8 w-full justify-between rounded-sm text-xs font-mono mt-0.5"
          data-testid="symbol-combobox"
        >
          <span className="truncate">{current?.label || value || "Select instrument"}</span>
          <ChevronsUpDown className="h-3.5 w-3.5 opacity-50 shrink-0" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[300px] p-0 rounded-sm" align="start">
        <Command>
          <CommandInput placeholder="Search Gold, BTC, Volatility, EURUSD…" className="h-9 text-xs font-mono" data-testid="symbol-search" />
          <CommandList className="max-h-[320px]">
            <CommandEmpty>No instrument found.</CommandEmpty>
            {Object.entries(groups).map(([market, items]) => (
              <CommandGroup key={market} heading={market}>
                {items.map((o) => (
                  <CommandItem
                    key={o.symbol}
                    value={`${o.symbol} ${o.label}`}
                    onSelect={() => {
                      onChange(o.symbol);
                      setOpen(false);
                    }}
                    className="text-xs font-mono"
                    data-testid={`symbol-option-${o.symbol}`}
                  >
                    <Check className={cn("mr-2 h-3.5 w-3.5 shrink-0", value === o.symbol ? "opacity-100" : "opacity-0")} />
                    <span className="truncate">{o.label}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
