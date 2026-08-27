/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      colors: {
        ink: {
          950: "#0b0e14",
          900: "#11151d",
          800: "#171c26",
          700: "#232937",
          600: "#323b4d",
          400: "#7d8698",
          200: "#c7cdda",
          100: "#e7eaf0",
        },
        accent: {
          DEFAULT: "#3f7cf7",
          dim: "#274a99",
        },
        good: "#3fbf7f",
        warn: "#e0a63c",
        bad: "#e0563c",
      },
    },
  },
  plugins: [],
};
