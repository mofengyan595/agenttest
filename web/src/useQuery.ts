import { useEffect, useState } from "react";
import { ApiError } from "./api/client";
export function useQuery<T>(loader: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<{
    data?: T;
    error?: string;
    loading: boolean;
  }>({ loading: true });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState({ loading: true });
    loader(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ data, loading: false });
      },
      (error) => {
        if (!controller.signal.aborted)
          setState({
            error:
              error instanceof ApiError
                ? error.message
                : "加载失败，请检查 API 服务后重试。",
            loading: false,
          });
      },
    );
    return () => controller.abort();
  }, [loader, revision]);
  return { ...state, retry: () => setRevision((v) => v + 1) };
}
