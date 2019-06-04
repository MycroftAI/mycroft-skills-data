pipeline {
    agent any

    stages {

        // Run an ETL job that will populate the database with the
        // contents of the skill-metadata.json file in the 18.08 branch
        stage('Load 19.02 skills') {
            when {
                branch '19.02'
                //changeset 'skill-metadata.json'
            }
            steps {
                echo 'Running ETL script against test environment...'
                sh 'ssh mycroft@138.197.73.71 "cd /opt/selene/selene-backend/batch; pipenv run python script/load_skill_display_data.py --core-version 19.02"'
                echo 'Running ETL script against production environment...'
                sh 'ssh mycroft@165.22.40.13 "cd /opt/selene/selene-backend/batch; pipenv run python script/load_skill_display_data.py --core-version 19.02"'
            }
        }
    }
}
