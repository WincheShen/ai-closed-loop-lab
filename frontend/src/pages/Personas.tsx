import { useEffect, useState } from 'react'

interface Persona {
  id: string
  name: string
  capital: number
}

interface Account {
  account_id: string
  persona_id: string
  persona_name: string
  capital: number
  available_cash: number
  total_value: number
  total_pnl: number
  created_at: string
  updated_at: string
}

export default function Personas() {
  const [personas, setPersonas] = useState<Persona[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [personasRes, accountsRes] = await Promise.all([
          fetch('/api/personas'),
          fetch('/api/accounts')
        ])
        const personasData = await personasRes.json()
        const accountsData = await accountsRes.json()
        setPersonas(personasData.personas || [])
        setAccounts(accountsData.accounts || [])
      } catch (error) {
        console.error('Failed to fetch personas/accounts:', error)
      } finally {
        setLoading(false)
      }
    }
    fetchData()
  }, [])

  if (loading) {
    return <div className="p-6">加载中...</div>
  }

  return (
    <div className="p-6">
      <h1 className="text-3xl font-bold mb-6">交易人格管理</h1>
      
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {personas.map((persona) => {
          const account = accounts.find(a => a.persona_id === persona.id)
          return (
            <div key={persona.id} className="border rounded-lg p-4 bg-white shadow-sm">
              <h2 className="text-xl font-bold mb-4">{persona.name}</h2>
              <div className="space-y-2">
                <div className="flex justify-between">
                  <span className="text-gray-600">人格ID:</span>
                  <span className="font-mono text-sm">{persona.id}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-600">初始资金:</span>
                  <span className="font-semibold">¥{persona.capital.toLocaleString()}</span>
                </div>
                {account && (
                  <>
                    <div className="flex justify-between">
                      <span className="text-gray-600">可用资金:</span>
                      <span className="font-semibold">¥{account.available_cash.toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-600">总价值:</span>
                      <span className="font-semibold">¥{account.total_value.toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-600">累计盈亏:</span>
                      <span className={`font-semibold ${account.total_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                        ¥{account.total_pnl.toLocaleString()}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-600">盈亏比例:</span>
                      <span className={`font-semibold ${account.total_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                        {((account.total_pnl / account.capital) * 100).toFixed(2)}%
                      </span>
                    </div>
                  </>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
