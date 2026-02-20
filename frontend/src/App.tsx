import { Routes, Route } from 'react-router-dom'
import Dashboard from './components/Dashboard'
import BuildDetail from './components/BuildDetail'
import KnownFailureDetail from './components/KnownFailureDetail'
import TriageLogs from './components/TriageLogs'
import Layout from './components/Layout'

function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/builds/:buildNumber" element={<BuildDetail />} />
        <Route path="/known-failures/:id" element={<KnownFailureDetail />} />
        <Route path="/logs" element={<TriageLogs />} />
      </Routes>
    </Layout>
  )
}

export default App
