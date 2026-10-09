import { useEffect, useState } from 'react'

export interface PersonaOption {
  id: string
  name: string
  enabled?: boolean
}

/**
 * 从 /api/personas 拉取人格下拉选项，避免各页面硬编码人格列表
 * 导致新增人格（如 ultra_short_momentum_v1 隔夜客）不显示。
 *
 * includeAll=true 时在头部追加「全部人格」选项。
 */
export function usePersonaOptions(includeAll = false) {
  const [options, setOptions] = useState<PersonaOption[]>(
    includeAll ? [{ id: 'all', name: '全部人格' }] : [],
  )

  useEffect(() => {
    fetch('/api/personas')
      .then((r) => r.json())
      .then((data) => {
        const list: PersonaOption[] = (data.personas || [])
          // 停用的人格仍出现在下拉里，方便回看其历史持仓/日报
          .map((p: any) => ({ id: p.id, name: p.name, enabled: p.enabled }))
        setOptions(includeAll ? [{ id: 'all', name: '全部人格' }, ...list] : list)
      })
      .catch((err) => console.error('Failed to load personas:', err))
  }, [includeAll])

  return options
}
