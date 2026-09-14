import { type PropsWithChildren } from "react";
import { QueryClientProvider, focusManager } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import * as api from "./client";
import { useStats, useToday } from "./hooks";
import { createQueryClient } from "./queryClient";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  focusManager.setFocused(undefined);
});

it("оновлює дату на найближчій межі хвилини й перераховує статистику після опівночі", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-11T20:59:50Z"));
  let day = "2026-09-11";
  vi.spyOn(api, "getToday").mockImplementation(async () => ({ day }));
  vi.spyOn(api, "listStats").mockResolvedValue([]);
  const client = createQueryClient();
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const { result } = renderHook(
    () => {
      useStats(true);
      return useToday(true);
    },
    { wrapper },
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10);
  });
  expect(result.current.data?.day).toBe("2026-09-11");
  day = "2026-09-12";
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10_000);
  });
  expect(result.current.data?.day).toBe("2026-09-12");
  expect(api.listStats).toHaveBeenCalledTimes(2);
  client.clear();
});

it("перевіряє дату при поверненні до вкладки", async () => {
  vi.spyOn(api, "getToday").mockResolvedValue({ day: "2026-09-11" });
  const client = createQueryClient();
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const { result } = renderHook(() => useToday(true), { wrapper });
  await waitFor(() => expect(result.current.data?.day).toBe("2026-09-11"));
  vi.mocked(api.getToday).mockResolvedValue({ day: "2026-09-12" });
  act(() => {
    focusManager.setFocused(false);
    focusManager.setFocused(true);
  });
  await waitFor(() => expect(result.current.data?.day).toBe("2026-09-12"));
  client.clear();
});
