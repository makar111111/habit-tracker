// Підключає додаткові перевірки для DOM: toBeInTheDocument, toHaveAttribute
// і подібні. Без цього рядка вони існують лише в типах, а в момент
// виклику виявляється, що такої функції немає.
import "@testing-library/jest-dom/vitest";
