import { Routes, Route } from 'react-router-dom'
import { Layout } from '@/components/layout/Layout'
import Dashboard from '@/pages/Dashboard'
import QuantMonitor from '@/pages/QuantMonitor'
import AgentWorkspace from '@/pages/AgentWorkspace'
import AgentDailyReport from '@/pages/AgentDailyReport'
import ContentPipeline from '@/pages/ContentPipeline'
import Strategy from '@/pages/Strategy'
import Analyze from '@/pages/Analyze'
import Records from '@/pages/Records'
import Portfolio from '@/pages/Portfolio'
import StrategyEvolution from '@/pages/StrategyEvolution'
import Personas from '@/pages/Personas'
import NotFound from '@/pages/NotFound'

function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout><Dashboard /></Layout>} />
      <Route path="/quant" element={<Layout><QuantMonitor /></Layout>} />
      <Route path="/agents" element={<Layout><AgentWorkspace /></Layout>} />
      <Route path="/agent-report" element={<Layout><AgentDailyReport /></Layout>} />
      <Route path="/content" element={<Layout><ContentPipeline /></Layout>} />
      <Route path="/strategy" element={<Layout><Strategy /></Layout>} />
      <Route path="/analyze" element={<Layout><Analyze /></Layout>} />
      <Route path="/records" element={<Layout><Records /></Layout>} />
      <Route path="/portfolio" element={<Layout><Portfolio /></Layout>} />
      <Route path="/strategy-evolution" element={<Layout><StrategyEvolution /></Layout>} />
      <Route path="/personas" element={<Layout><Personas /></Layout>} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}

export default App
