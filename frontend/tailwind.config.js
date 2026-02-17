/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'github-green': '#238636',
        'github-purple': '#8957e5',
        'github-red': '#f85149',
        'github-yellow': '#d29922',
        'gray-750': '#2d3748',
        'gray-850': '#1a202c',
      },
    },
  },
  plugins: [],
}
