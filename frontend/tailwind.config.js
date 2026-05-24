/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class'],
  content: [
    './pages/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './app/**/*.{ts,tsx}',
    './src/**/*.{ts,tsx}',
  ],
  theme: {
    container: {
      center: true,
      padding: '2rem',
      screens: {
        '2xl': '1400px',
      },
    },
    extend: {
      colors: {
        // FinTech 暗黑主题基础色
        background: '#0a0a0f',
        foreground: '#e2e8f0',
        panel: '#12121a',
        'panel-border': '#1e1e2e',
        'panel-hover': '#1a1a28',
        
        // 强调色 - 荧光蓝 (AI 状态)
        accent: {
          DEFAULT: '#00f0ff',
          glow: 'rgba(0, 240, 255, 0.15)',
          muted: 'rgba(0, 240, 255, 0.4)',
        },
        
        // 金融红绿涨跌色
        bullish: '#00c853',
        'bullish-glow': 'rgba(0, 200, 83, 0.15)',
        bearish: '#ff1744',
        'bearish-glow': 'rgba(255, 23, 68, 0.15)',
        
        // 状态色
        status: {
          running: '#00c853',
          idle: '#78909c',
          error: '#ff1744',
          warning: '#ffab00',
        },
        
        // 灰度层级
        muted: {
          DEFAULT: '#64748b',
          foreground: '#94a3b8',
        },
        
        // shadcn 兼容色板
        border: '#1e1e2e',
        input: '#1e1e2e',
        ring: '#00f0ff',
        primary: {
          DEFAULT: '#00f0ff',
          foreground: '#0a0a0f',
        },
        secondary: {
          DEFAULT: '#1e1e2e',
          foreground: '#e2e8f0',
        },
        destructive: {
          DEFAULT: '#ff1744',
          foreground: '#ffffff',
        },
        card: {
          DEFAULT: '#12121a',
          foreground: '#e2e8f0',
        },
        popover: {
          DEFAULT: '#12121a',
          foreground: '#e2e8f0',
        },
      },
      borderRadius: {
        lg: '0.5rem',
        md: '0.375rem',
        sm: '0.25rem',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      boxShadow: {
        'glow-accent': '0 0 20px rgba(0, 240, 255, 0.15)',
        'glow-bullish': '0 0 20px rgba(0, 200, 83, 0.15)',
        'glow-bearish': '0 0 20px rgba(255, 23, 68, 0.15)',
      },
      animation: {
        'pulse-glow': 'pulse-glow 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'slide-in': 'slide-in 0.3s ease-out',
        'fade-in': 'fade-in 0.2s ease-out',
      },
      keyframes: {
        'pulse-glow': {
          '0%, 100%': { opacity: '1', boxShadow: '0 0 10px rgba(0, 240, 255, 0.2)' },
          '50%': { opacity: '0.7', boxShadow: '0 0 20px rgba(0, 240, 255, 0.4)' },
        },
        'slide-in': {
          '0%': { transform: 'translateX(-10px)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '1' },
        },
        'fade-in': {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
}
