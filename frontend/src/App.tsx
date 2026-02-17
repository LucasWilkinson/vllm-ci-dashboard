import { Routes, Route } from 'react-router-dom'
import Dashboard from './components/Dashboard'
import BuildDetail from './components/BuildDetail'
import Layout from './components/Layout'

function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/builds/:buildNumber" element={<BuildDetail />} />
      </Routes>
    </Layout>
  )
}

export default App
