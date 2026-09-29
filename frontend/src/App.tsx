import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { applyTheme, useThemeStore } from "@/stores/themeStore";

/*
 * Query defaults chosen for this app's actual latency profile: real answers
 * take 20-45s because generation is CPU-only, so staleTime is generous and
 * refetchOnWindowFocus is off - neither would ever usefully fire here, and both
 * would only duplicate expensive in-flight requests.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

/*
 * Applied at module scope so `data-theme` is on <html> before React mounts,
 * avoiding a flash of the wrong palette. themeStore applies the same value again
 * on its first read, which is a no-op.
 */
applyTheme(useThemeStore.getState().theme);

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppShell />
    </QueryClientProvider>
  );
}
