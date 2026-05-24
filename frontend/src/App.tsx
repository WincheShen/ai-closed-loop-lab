import { Routes, Route } from 'react-router-dom'
import { Layout } from '@/components/layout/Layout'
import Dashboard from '@/pages/Dashboard'
import QuantMonitor from '@/pages/QuantMonitor'
import AgentWorkspace from '@/pages/AgentWorkspace'
import ContentPipeline from '@/pages/ContentPipeline'
import Strategy from '@/pages/Strategy'
import Analyze from '@/pages/Analyze'
import Records from '@/pages/Records'

function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/quant" element={<QuantMonitor />} />
        <Route path="/agents" element={<AgentWorkspace />} />
        <Route path="/content" element={<ContentPipeline />} />
        <Route path="/strategy" element={<Strategy />} />
        <Route path="/analyze" element={<Analyze />} />
        <Route path="/records" element={<Records />} />
      </Routes>
    </Layout>
  )
}

export default App
