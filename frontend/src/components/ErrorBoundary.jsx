import { Component } from "react";
import { Button } from "@/components/ui/button";
import { RefreshCw } from "lucide-react";

// Catches any render/runtime error so a transient failure (e.g. a chart glitch)
// shows a recoverable fallback instead of blanking the whole app to a black screen.
// It AUTO-RECOVERS: because the dashboard refetches data every few seconds, most
// glitches are transient, so we silently retry a couple of times before ever
// bothering the user with a manual reload.
const MAX_AUTO_RETRIES = 3;

export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, retries: 0 };
    this._timer = null;
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error("App error boundary caught:", error, info);
    if (this.state.retries < MAX_AUTO_RETRIES) {
      // transient glitch -> self-heal shortly without the user doing anything
      this._timer = setTimeout(() => {
        this.setState((s) => ({ hasError: false, retries: s.retries + 1 }));
      }, 1200);
    }
  }

  componentWillUnmount() {
    if (this._timer) clearTimeout(this._timer);
  }

  render() {
    if (this.state.hasError) {
      const willRetry = this.state.retries < MAX_AUTO_RETRIES;
      return (
        <div
          className="min-h-screen grid place-items-center bg-background px-6"
          data-testid="app-error-boundary"
        >
          <div className="max-w-sm text-center space-y-4">
            <RefreshCw
              className={`h-6 w-6 mx-auto text-muted-foreground ${willRetry ? "animate-spin" : ""}`}
            />
            <h1 className="text-xl font-mono font-semibold text-foreground">
              {willRetry ? "Reconnecting…" : "Something hiccuped"}
            </h1>
            <p className="text-sm text-muted-foreground">
              {willRetry
                ? "A temporary glitch — recovering automatically. Your data is safe."
                : "The app hit a temporary glitch. Your data is safe — reload to pick up where you left off."}
            </p>
            {!willRetry && (
              <Button
                onClick={() => window.location.reload()}
                data-testid="error-reload-btn"
                className="gap-2"
              >
                <RefreshCw className="h-4 w-4" /> Reload
              </Button>
            )}
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
