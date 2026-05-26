import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatNumber(num: number, decimals = 2): string {
  if (num >= 1e8) return (num / 1e8).toFixed(decimals) + '亿'
  if (num >= 1e4) return (num / 1e4).toFixed(decimals) + '万'
  return num.toFixed(decimals)
}

export function formatPercent(num: number, decimals = 2): string {
  const sign = num > 0 ? '+' : ''
  return `${sign}${num.toFixed(decimals)}%`
}

export function formatLatency(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}
