/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        serif: ['Newsreader', 'Georgia', 'serif'],
        sans:  ['DM Sans', 'sans-serif'],
        mono:  ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      colors: {
        paper:       'var(--paper)',
        'paper-2':   'var(--paper-2)',
        'paper-3':   'var(--paper-3)',
        rule:        'var(--rule)',
        'rule-soft': 'var(--rule-soft)',
        ink:         'var(--ink)',
        'ink-2':     'var(--ink-2)',
        'ink-soft':  'var(--ink-soft)',
        'ink-mute':  'var(--ink-mute)',
        accent:      'var(--accent)',
        'accent-ink':'var(--accent-ink)',
        gold:        'var(--gold)',
        green:       'var(--green)',
      },
      borderRadius: {
        none: '0',
      },
    },
  },
  plugins: [],
}
