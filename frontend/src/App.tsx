import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import FoodsPage from './pages/FoodsPage'
import RecipesPage from './pages/RecipesPage'
import MealLogPage from './pages/MealLogPage'
import TissueAdminPage from './pages/TissueAdminPage'
import TrainingPage from './pages/TrainingPage'

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Layout />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/foods" element={<FoodsPage />} />
        <Route path="/recipes" element={<RecipesPage />} />
        <Route path="/log" element={<MealLogPage />} />
        <Route path="/training" element={<TrainingPage />} />
        <Route path="/tissues" element={<TissueAdminPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
