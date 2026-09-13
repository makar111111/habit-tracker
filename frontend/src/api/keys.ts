/** Префікси дозволяють оновити активні й архівні варіанти одним запитом. */
export const keys = {
  me: ["me"] as const,
  today: ["today"] as const,
  habitChange: ["habit-change"] as const,
  habits: ["habits"] as const,
  allHabits: ["habits", "all"] as const,
  stats: ["stats"] as const,
  allStats: ["stats", "all"] as const,
  checkins: (habitId: number, since: string) => ["checkins", habitId, since] as const,
  checkinsOf: (habitId: number) => ["checkins", habitId] as const,
};
