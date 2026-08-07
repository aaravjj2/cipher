import { readLocal, writeLocal } from "@/lib/localStorage";
import type { ChartSaveCard } from "@/types/cipher";

const STORAGE_KEY = "cipher_chart_saves_v1";

export function loadChartSaves(): ChartSaveCard[] {
  return readLocal<ChartSaveCard[]>(STORAGE_KEY, []);
}

function saveAll(cards: ChartSaveCard[]): void {
  writeLocal(STORAGE_KEY, cards);
}

/** Appends a new save (called from Night Vision's "Save chart" button) and returns the updated list. */
export function addChartSave(entry: Omit<ChartSaveCard, "id" | "dateAdded">): ChartSaveCard[] {
  const cards = loadChartSaves();
  const next: ChartSaveCard = {
    ...entry,
    id: `cs-${Date.now()}-${Math.round(Math.random() * 1e6)}`,
    dateAdded: new Date().toLocaleDateString("en-US", { month: "numeric", day: "numeric", year: "2-digit" }),
  };
  const updated = [next, ...cards];
  saveAll(updated);
  return updated;
}

export function removeChartSave(id: string): ChartSaveCard[] {
  const updated = loadChartSaves().filter((c) => c.id !== id);
  saveAll(updated);
  return updated;
}
