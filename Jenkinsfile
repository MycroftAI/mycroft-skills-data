pipeline {
    agent any

    stages {

        // Run an ETL job that will populate the database with the
        // contents of the skill-metadata.json file in the 18.08 branch.
        stage('Load 19.02 skills') {
            when {
                branch '19.02'
                //changeset 'skill-metadata.json'
            }
            steps {
                echo 'Running ETL script...'
                sh '''
                    ssh mycroft@165.22.40.13 << EOF
                        cd /opt/selene/selene-backend/batch/
                        pipenv run python script/load_skill_data.py --core-version 19.02
                    EOF
                '''
            }
        }
    }
}
