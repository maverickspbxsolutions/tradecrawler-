import { Component } from "react";
import { Button } from "@/components/ui/button";
import { RefreshCw } from "lucide-react";

// Catches any render/runtime error so a transient failure (e.g. a chart glitch)
// shows a recoverable fallback instead of blanking the whole app to a black screen.
export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error("App error boundary caught:", error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          className="min-h-screen grid place-items-center bg-background px-6"
          data-testid="app-error-boundary"
        >
          <div className="max-w-sm text-center space-y-4">
            <h1 className="text-xl font-mono font-semibold text-foreground">
              Something hiccuped
            </h1>
            <p className="text-sm text-muted-foreground">
              The app hit a temporary glitch. Your data is safe — reload to pick up where you left off.
            </p>
            <Button
              onClick={() => window.location.reload()}
              data-testid="error-reload-btn"
              className="gap-2"
            >
              <RefreshCw className="h-4 w-4" /> Reload
            </Button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
