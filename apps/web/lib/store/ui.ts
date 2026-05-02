import { create } from "zustand";

/** 全局面壳：侧栏折叠、Agent 观察区（右栏）展开 — 与 Manus 式「中间小窗 ↔ 右栏」一致。 */
interface UiState {
  sidebarCollapsed: boolean;
  /** true：右侧显示完整 Live Computer；false：仅中间底部显示小窗入口 */
  liveComputerExpanded: boolean;
  setSidebarCollapsed: (v: boolean) => void;
  toggleSidebar: () => void;
  setLiveComputerExpanded: (v: boolean) => void;
  openLiveComputer: () => void;
  closeLiveComputer: () => void;
}

export const useUiStore = create<UiState>((set) => ({
  sidebarCollapsed: false,
  liveComputerExpanded: false,
  setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setLiveComputerExpanded: (v) => set({ liveComputerExpanded: v }),
  openLiveComputer: () => set({ liveComputerExpanded: true }),
  closeLiveComputer: () => set({ liveComputerExpanded: false }),
}));
