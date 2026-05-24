import { Routes, Route } from 'react-router-dom'
import { Layout } from '@/components/layout/Layout'
import Dashboard from '@/pages/Dashboard'
import QuantMonitor from '@/pages/QuantMonitor'
import AgentWorkspace from '@/pages/AgentWorkspace'
import ContentPipeline from '@/pages/ContentPipeline'

function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/quant" element={<QuantMonitor />} />
        <Route path="/agents" element={<AgentWorkspace />} />
        <Route path="/content" element={<ContentPipeline />} />
      </Routes>
    </Layout>
  )
}

export default App
