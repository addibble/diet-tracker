import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import AccountPage from './pages/AccountPage'
import AdminPage from './pages/AdminPage'
import DashboardPage from './pages/DashboardPage'
import DatabasePage from './pages/DatabasePage'
import MealLogPage from './pages/MealLogPage'
import TrainingPage from './pages/TrainingPage'

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/invite/:token" element={<RegisterPage />} />
      <Route element={<Layout />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/database" element={<DatabasePage />} />
        <Route path="/foods" element={<Navigate to="/database?tab=foods" replace />} />
        <Route path="/recipes" element={<Navigate to="/database?tab=recipes" replace />} />
        <Route path="/tissues" element={<Navigate to="/database?tab=tissues" replace />} />
        <Route path="/log" element={<MealLogPage />} />
        <Route path="/training" element={<TrainingPage />} />
        <Route path="/account" element={<AccountPage />} />
        <Route path="/admin" element={<AdminPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
