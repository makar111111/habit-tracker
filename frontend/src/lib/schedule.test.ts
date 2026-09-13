import { describe, expect, it } from "vitest";

import { pluralCompletions } from "./schedule";

describe("Відмінювання запланованих виконань", () => {
  it.each([
    [1, "заплановане виконання"], [2, "заплановані виконання"], [5, "запланованих виконань"],
    [11, "запланованих виконань"], [14, "запланованих виконань"], [21, "заплановане виконання"],
    [22, "заплановані виконання"], [25, "запланованих виконань"],
  ])("%i: %s", (count, expected) => expect(pluralCompletions(Number(count))).toBe(expected));
});
